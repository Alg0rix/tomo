# Tomo Tool Permissions + Clarify — Design

**Date:** 2026-07-31  
**Status:** Approved (pipeline, modules, HITL/UI)  
**Inspiration:** Hermes `check_all_command_guards` + clarify tool; Kimi mode naming; Evonic HITL wait  
**Goal:** Hermes-like approval modes with Tomo-specific workplace escape, jail lift, async HITL, and web UI — without a 4k-line godfile.

---

## 1. Goal

When tools leave the workplace or look dangerous, Tomo **assesses → (smart) → asks the user → once/session/always/deny**, or runs freely in **`off`**. Hardline and user deny never run. Clarify becomes a real blocking question with optional choices. File tools may touch outside-root paths only under an explicit per-call grant.

---

## 2. Decisions (locked)

| Topic | Choice |
|-------|--------|
| Modes | `manual` \| `smart` \| `off` (Hermes); session toggle may force `off` like `/yolo` |
| What requires approval | Workplace **escape** + **dangerous** patterns (not every in-root bash) |
| File tools outside root | Allowed after approve / in `off` (option B) — via per-call jail grant |
| Clarify | Blocking; question + ≤4 choices; UI adds free-text Other |
| User choices | once / session / always / deny |
| Hardline in `off` | Yes — hardline + `approvals.deny` never bypassable |
| Architecture | Central gate in `run_turn` (not per-tool copy-paste) |
| Tirith / external scanner | **Out of v1** — pattern + escape only |
| UI v1 | Web chat (`chat.js` + SSE + resolve APIs); registry ready for other channels |
| Async | `asyncio.Event` waiter (no Hermes-style sync thread block in the loop) |

---

## 3. Architecture

```text
run_turn
  └─ for each tool call:
       gate.decide(tool, args, work_root, mode)
         ├─ hardline / user_deny → Block(message) → tool_result
         ├─ off / allowlist / no findings → Allow(+optional grant)
         ├─ smart → aux LLM approve|deny|escalate
         └─ HITL → yield approval_required → wait → Allow|Block
       clarify tool → yield clarify_required → wait → tool_result(JSON answer)
       execute(tool) under outside_grant ContextVar
            └─ jail_path honors grant
SSE / chat.js cards ←→ POST /api/approvals/{id} | /api/clarify/{id}
```

**Consent contract (from Hermes):** deny and timeout tell the model not to retry, rephrase, or achieve the same outcome via another path. Silence is not consent.

---

## 4. Components & file map

### New package `app/runtime/permissions/`

| Path | Responsibility |
|------|----------------|
| `modes.py` | Resolve effective mode from config + session override |
| `patterns.py` | Hardline + dangerous regexes (trimmed Hermes port; Tomo-tuned) |
| `escape.py` | Out-of-root detection for file args; cheap bash/runpy path heuristics |
| `assess.py` | `assess(tool, args, work_root) -> Assessment` (findings + allowlist keys) |
| `allowlist.py` | Session + permanent allowlists under `$TOMO_HOME` |
| `smart.py` | Aux LLM risk review (strip `#` comments; XML-wrap untrusted input) |
| `hitl.py` | Pending approval/clarify registry; asyncio wait; timeout fail-closed |
| `gate.py` | Pipeline orchestration → `Allow` / `Block` / wait handle |
| `grants.py` | ContextVar `outside_grant`; helpers to set/clear around one execute |

### Touched existing files

| Path | Change |
|------|--------|
| `app/runtime/agent/loop.py` | Call gate before `execute`; yield HITL events; wire clarify |
| `app/runtime/tools/sandbox.py` | `jail_path` accepts active grant |
| `app/runtime/tools/bash.py` / `runpy.py` | No content jail still; gate handles risk/escape before run |
| `app/runtime/tools/clarify.py` | Register pending clarify; return user answer JSON (not stub `CLARIFY:`) |
| `app/tools/clarify.json` | Schema: `question` + optional `choices` (max 4) |
| `app/channels/sse_map.py` | Map `approval_required` / `clarify_required` |
| `app/api/…` | Resolve endpoints for approval + clarify |
| `app/static/js/chat.js` (+ CSS) | Inline cards, buttons, waiting state, mode display |
| `app/core/config.py` or settings store | `approvals.mode`, `approvals.timeout`, `approvals.deny` |

**Do not** collapse permissions into one Hermes-sized file. Prefer ~150–250 lines per module; split before ~400.

---

## 5. Pipeline (normative)

```text
1. assess(tool, args, work_root) → findings[]
2. any hardline?           → BLOCK (never bypass)
3. any user_deny match?    → BLOCK (never bypass)
4. mode == off?            → ALLOW (+ escape grant if escape finding or always for off)
5. allowlist hit (all keys)? → ALLOW (+ grant if escape)
6. no findings?            → ALLOW
7. mode == smart?
     → aux LLM on findings
     → APPROVE: one-shot ALLOW (do not permanent-allowlist on smart approve)
     → DENY + interactive: escalate to HITL with smart_denied (once|deny only)
     → DENY + non-interactive: BLOCK
     → ESCALATE: HITL
8. mode == manual (or escalated): HITL once|session|always|deny
9. on ALLOW with escape: set outside_grant for execute; clear after
```

### Finding kinds

| Kind | Meaning |
|------|---------|
| `hardline` | Catastrophic; never run via agent |
| `user_deny` | Matches `approvals.deny` globs |
| `escape` | Path/command leaves workplace root |
| `dangerous` | Recoverable-risk pattern (rm -r, curl\|sh, sensitive redirects, …) |

### Allowlist keys (finer than Hermes description-only)

- Escape: `escape:<normalized-root-or-path-prefix>` (e.g. `escape:~/.tomo`)
- Dangerous: `dangerous:<stable_id>` (e.g. `dangerous:recursive_delete`)
- “Always” persists permanent keys; “Session” session-scoped; “Once” neither
- Smart APPROVE never writes permanent/session keys (one command/tool-call only)
- When `smart_denied`, UI offers **Once / Deny** only

### Escape detection

**File tools** (`read_file`, `write_file`, `list_dir`, `delete_file`, `search_files`, `str_replace`, `patch`): resolve path; if not under `work_root` → `escape` finding (do not error in `jail_path` yet — gate decides).

**bash / runpy:** heuristic scan of command/code for:
- `~` / `$HOME` / `${HOME}`
- absolute paths outside `work_root`
- `cd` to outside root  
False negatives possible (obfuscation); hardline/dangerous still apply. V1 accepts heuristic quality over a full shell parser.

### Jail lift

- ContextVar: `outside_grant: frozenset[Path] | Literal["*"] | None`
- Approved escape → grant specific resolved path(s) for that call
- `mode=off` → grant `*` for the call when any escape would have been needed, or always allow jail bypass while `off` is active for that execute
- `jail_path`: under root **or** under grant **or** grant is `*`
- Grant **must** be cleared in `finally` after the tool returns

---

## 6. Clarify

- Tool args: `question: str`, `choices?: string[]` (max 4)
- Loop treats `clarify` specially: no FS assess; emit `clarify_required`; wait; return JSON:

```json
{"question":"…","choices_offered":["…"],"user_response":"…"}
```

- In `off`: clarify still works (Hermes-like; unlike Kimi auto). Unattended jobs that must not block should avoid calling clarify.
- Do **not** use clarify for dangerous-command confirm — the approval gate owns that.

---

## 7. HITL + SSE + UI

### Loop events

- `approval_required`: `{id, tool, args_preview, findings[], choices, smart_denied?}`
- `clarify_required`: `{id, question, choices[]}`

Args preview is **redacted** for secrets (reuse or add a small redactor). Agent does not see a vague “pending” forever: after resolve it gets success output or an explicit `BLOCKED:…` tool result.

### HTTP

- `POST /api/approvals/{id}` body `{ "choice": "once"|"session"|"always"|"deny", "reason"?: string }`
- `POST /api/clarify/{id}` body `{ "answer": string }`
- Unknown/expired id → 404; already resolved → 409

### Timeout

- Default 300s (config `approvals.timeout`)
- Timeout → deny / empty clarify answer with fail-closed message to the model

### Web UI (`chat.js`)

- Inline card in the stream (not a toast): tool name, finding reasons, redacted preview
- Approval buttons: Once / Session / Always / Deny (respect `smart_denied` / persistable flags)
- Clarify: choice buttons + text Other
- While waiting: show waiting state; disable conflicting send or clearly queue
- Surface current mode; allow change via settings and/or slash `/manual` `/smart` `/off`

### Other channels

V1 ships web only. `hitl.py` is channel-agnostic so Telegram/etc. can resolve the same `id` later.

---

## 8. Config

Stored under Tomo settings / `$TOMO_HOME` (exact key path follows existing settings patterns):

```yaml
approvals:
  mode: smart          # manual | smart | off
  timeout: 300         # seconds
  deny: []             # fnmatch globs on command/code or "tool:path" forms
```

Permanent allowlist file: e.g. `$TOMO_HOME/approvals_allowlist.json` (or equivalent). Session allowlist is in-memory keyed by session id.

---

## 9. Error handling & model-facing messages

| Outcome | Tool result (to model) |
|---------|------------------------|
| Hardline | `BLOCKED (hardline): … cannot be executed via the agent …` |
| User deny | `BLOCKED: matches deny rule '…' …` |
| User deny choice / timeout | `BLOCKED: … Do NOT retry, rephrase, or achieve the same outcome via a different path.` |
| Smart deny (non-interactive) | `BLOCKED by smart approval: …` |
| Clarify timeout | JSON with `user_response` null/empty + note that user did not answer |

Never raise out of the gate into the loop uncaught; always a string tool result.

---

## 10. Testing

| Area | Tests |
|------|-------|
| `patterns` / hardline | Known catastrophic cmds block; benign `ls` does not |
| `escape` | Relative in-root OK; `~/.tomo` / absolute outside → escape |
| `jail_path` + grant | Without grant rejects; with grant allows; grant cleared |
| `gate` modes | manual asks; smart APPROVE one-shot; off skips ask; hardline in off still blocks |
| `allowlist` | once vs session vs always persistence |
| `clarify` | Wait + resolve returns JSON |
| Loop / SSE (light) | Event shapes for approval_required / clarify_required |

Prefer unit tests on `permissions/*` without a live LLM; mock `smart.py`.

---

## 11. Non-goals (v1)

- Tirith or external binary scanners
- Full POSIX shell parsing / deobfuscation suite (Hermes-level)
- Messaging-channel approval buttons (API ready only)
- Plan-mode write gating (Evonic-style) as a separate system
- Changing default cwd away from workplace root

---

## 12. Better than Hermes (checklist)

| Hermes pain | Tomo approach |
|-------------|----------------|
| Monolithic `approval.py` | Split `permissions/` modules |
| No workplace-escape class | First-class `escape` finding |
| File tools separate / often blocked | Same gate + explicit jail grant |
| Sync gateway thread wait | Async HITL in agent loop |
| Allowlist key = pattern description | Stable ids + path-prefix escape keys |
| Tirith coupled in hot path | Optional later; not required for v1 |

---

## 13. Implementation order (for the plan)

1. `permissions/` core: patterns, escape, assess, grants, allowlist, gate (manual/off)
2. Wire `jail_path` + loop gate (block/allow without UI — auto-deny if no waiter in tests)
3. `hitl.py` + API resolve + SSE events
4. `chat.js` approval + clarify cards
5. `smart.py` + mode settings / slash
6. Clarify tool schema + backend rewrite
7. Tests for the matrix above
