# Reasoning Effort Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add model-specific reasoning-effort options to LLM profiles, expose a persistent per-session selector in the chat composer, and forward the selected provider value on every compatible LLM request.

**Architecture:** Store ordered effort values as JSON on each LLM profile and the user's selected value as a column on each session. A session API resolves the coordinator profile's options and validates updates; runtime selection falls back to the current target profile's highest option when a delegated model uses a different vocabulary. The shared composer loads and saves the session setting, while `OpenAICompatClient` conditionally adds the exact selected string to stream and non-stream payloads.

**Tech Stack:** Python 3.12, FastAPI, Pydantic, SQLite, OpenAI-compatible SDK, vanilla JavaScript, Jinja templates, pytest/httpx.

## Global Constraints

- Profile entries are provider-facing strings and must be sent exactly as configured in `reasoning_effort`.
- The last configured profile entry is the default/highest effort.
- Empty effort lists omit `reasoning_effort` and hide the composer selector.
- A session's selected value persists in SQLite and is scoped to the authenticated session owner.
- Existing databases must migrate idempotently with empty defaults.
- Do not change the existing API-key encryption/masking contract.
- Keep the existing `.gitignore` working-tree change untouched.

## File Map

- Modify `app/models/schema.py` — add initial DDL columns and idempotent migrations.
- Modify `app/models/mixins/llm_profiles.py` — normalize and persist profile effort lists and resolve valid/default values.
- Modify `app/models/mixins/sessions.py` — expose and persist the session effort value.
- Modify `app/services/store.py` — provide locked session-effort payload, validation, and runtime resolution methods.
- Modify `app/schemas/models.py` and `app/schemas/__init__.py` — validate profile effort lists and session update bodies.
- Modify `app/api/rest.py` — add authenticated GET/PUT session effort endpoints.
- Modify `app/runtime/llm/openai_compat.py` — forward the optional effort in both request modes.
- Modify `app/runtime/llm/__init__.py` and `app/runtime/agent/loop.py` — select the effort for session turns and per-agent fallback.
- Modify `app/templates/partials/settings/models.html` and `app/static/js/system.js` — edit profile options and show counts.
- Modify `app/templates/partials/chat_composer.html`, `app/static/css/tomo.css`, and `app/static/js/chat.js` — add the model/effort popover, loading, and persistence.
- Add/modify `tests/unit/models/test_llm_profiles.py`, `tests/unit/models/test_sessions_messages.py`, `tests/integration/test_llm_profiles_api.py`, `tests/unit/runtime/llm/test_openai_compat.py`, and `tests/unit/runtime/llm/test_factory.py` — cover storage, API, payload, and runtime behavior.

---

### Task 1: Add profile and session persistence primitives

**Files:**
- Modify: `app/models/schema.py`
- Modify: `app/models/mixins/llm_profiles.py`
- Modify: `app/models/mixins/sessions.py`
- Modify: `app/services/store.py`
- Test: `tests/unit/models/test_llm_profiles.py`
- Test: `tests/unit/models/test_sessions_messages.py`

**Interfaces:**
- `normalize_reasoning_efforts(value: object) -> list[str]` trims, removes empty values, and preserves first occurrence order.
- `effective_reasoning_effort(profile: dict | None, selected: str | None) -> str | None` returns a supported stored value, otherwise the profile's final configured value, otherwise `None`.
- `store.get_session_reasoning_effort(session_id: str) -> dict[str, Any] | None` returns the API-ready payload for the owned session lookup layer.
- `store.set_session_reasoning_effort(session_id: str, value: str | None) -> dict[str, Any] | None` validates against the coordinator profile and persists the raw selected value.
- `store.resolve_session_reasoning_effort(session_id: str, agent_id: str | None = None) -> str | None` resolves the stored session value against the target agent's profile for runtime use.

- [ ] **Step 1: Write the failing profile/session tests**

Add these behaviors to the existing test modules:

```python
def test_profile_reasoning_efforts_are_normalized_and_default_to_last(tmp_path) -> None:
    _rebind(tmp_path)
    profile = store.create_llm_profile({
        "id": "p",
        "name": "P",
        "api_key": "sk-p",
        "model": "model-a",
        "reasoning_efforts": [" low ", "", "high", "low"],
    })
    assert profile["reasoning_efforts"] == ["low", "high"]
    assert store.resolve_llm_profile(None)["reasoning_efforts"] == ["low", "high"]


def test_session_reasoning_effort_persists_and_falls_back_when_profile_changes(tmp_path) -> None:
    _rebind(tmp_path)
    store.create_llm_profile({
        "id": "default", "name": "D", "api_key": "sk-d", "model": "model-a",
        "reasoning_efforts": ["low", "high"],
    })
    store.set_default_llm_profile("default")
    sid = store.create_swarm_session(["main"], user_id="web")

    state = store.get_session_reasoning_effort(sid)
    assert state["reasoning_efforts"] == ["low", "high"]
    assert state["reasoning_effort"] == "high"
    store.set_session_reasoning_effort(sid, "low")
    assert store.get_session(sid)["reasoning_effort"] == "low"
    assert store.resolve_session_reasoning_effort(sid, "main") == "low"

    store.update_llm_profile("default", {"reasoning_efforts": ["minimal", "max"]})
    assert store.resolve_session_reasoning_effort(sid, "main") == "max"


def test_session_reasoning_effort_rejects_unknown_value(tmp_path) -> None:
    _rebind(tmp_path)
    store.create_llm_profile({
        "id": "default", "name": "D", "api_key": "sk-d", "model": "model-a",
        "reasoning_efforts": ["low", "high"],
    })
    store.set_default_llm_profile("default")
    sid = store.create_swarm_session(["main"])
    with pytest.raises(ValueError, match="reasoning effort"):
        store.set_session_reasoning_effort(sid, "unsupported")
```

- [ ] **Step 2: Run the focused tests and verify the expected failure**

Run:

```bash
pytest tests/unit/models/test_llm_profiles.py tests/unit/models/test_sessions_messages.py -k reasoning -v
```

Expected: FAIL because the schema, profile field, and store methods do not exist yet.

- [ ] **Step 3: Add schema columns and migrations**

Add `reasoning_efforts_json TEXT NOT NULL DEFAULT '[]'` to `llm_profiles` and `reasoning_effort TEXT NOT NULL DEFAULT ''` to `sessions` in the initial DDL. In `migrate()`, inspect each table with `PRAGMA table_info` and execute these idempotent statements when needed:

```python
if "reasoning_efforts_json" not in profile_cols:
    conn.execute(
        "ALTER TABLE llm_profiles ADD COLUMN reasoning_efforts_json TEXT NOT NULL DEFAULT '[]'"
    )
if "reasoning_effort" not in sess_cols:
    conn.execute(
        "ALTER TABLE sessions ADD COLUMN reasoning_effort TEXT NOT NULL DEFAULT ''"
    )
```

- [ ] **Step 4: Implement profile list normalization and storage**

In `llm_profiles.py`, use `json.loads` defensively when reading the JSON column and return `reasoning_efforts` as a list in both public and decrypted profile dictionaries. Normalize writes with `normalize_reasoning_efforts`; include `reasoning_efforts_json` in create inserts and update it whenever the update payload contains `reasoning_efforts`. Keep blank API-key behavior unchanged.

Implement the fallback helper as:

```python
def effective_reasoning_effort(profile, selected):
    efforts = list((profile or {}).get("reasoning_efforts") or [])
    selected = (selected or "").strip()
    if selected and selected in efforts:
        return selected
    return efforts[-1] if efforts else None
```

- [ ] **Step 5: Implement session serialization and store methods**

Include `reasoning_effort` in `_session_to_dict`. Add `set_session_reasoning_effort()` to the session mixin to update the column and `updated_at`. Add the three locked `Store` methods from the interface block. Resolve the coordinator profile for the API payload, return `reasoning_efforts`, `default_reasoning_effort`, `selected_reasoning_effort`, `reasoning_effort`, `profile_id`, and `model`, and use `effective_reasoning_effort()` for the effective value. For runtime resolution, use the session's stored value and resolve the requested target agent profile so delegated models fall back to their own last option.

- [ ] **Step 6: Run the focused tests and verify they pass**

Run:

```bash
pytest tests/unit/models/test_llm_profiles.py tests/unit/models/test_sessions_messages.py -k reasoning -v
```

Expected: PASS, with old profile/session tests still passing in the same files.

- [ ] **Step 7: Commit the persistence slice**

```bash
git add app/models/schema.py app/models/mixins/llm_profiles.py app/models/mixins/sessions.py app/services/store.py tests/unit/models/test_llm_profiles.py tests/unit/models/test_sessions_messages.py
git commit -m "feat: persist reasoning effort per profile and session"
```

### Task 2: Expose validated session effort API

**Files:**
- Modify: `app/schemas/models.py`
- Modify: `app/schemas/__init__.py`
- Modify: `app/api/rest.py`
- Test: `tests/integration/test_llm_profiles_api.py`
- Test: `tests/integration/test_session_isolation_api.py`

**Interfaces:**
- `ReasoningEffortUpdate(reasoning_effort: str | None = None)` accepts a provider value or `null`/blank to clear the stored override.
- `LLMProfileCreate.reasoning_efforts: list[str]` defaults to `[]`; `LLMProfileUpdate.reasoning_efforts: list[str] | None` changes the list only when supplied.
- `GET /api/sessions/{session_id}/reasoning-effort` returns the payload from `store.get_session_reasoning_effort()`.
- `PUT /api/sessions/{session_id}/reasoning-effort` returns the updated payload and responds `400` for a value not in the active coordinator profile's list.

- [ ] **Step 1: Write failing API tests**

Add to the integration tests:

```python
def test_session_reasoning_effort_api_round_trip(tmp_path) -> None:
    client = _client(tmp_path)
    try:
        store.create_llm_profile({
            "id": "default", "name": "D", "api_key": "sk-d", "model": "model-a",
            "reasoning_efforts": ["balanced", "deep"],
        })
        store.set_default_llm_profile("default")
        sid = store.create_swarm_session(["main"], user_id="web")

        res = client.get(f"/api/sessions/{sid}/reasoning-effort")
        assert res.status_code == 200
        assert res.json()["reasoning_effort"] == "deep"

        res = client.put(
            f"/api/sessions/{sid}/reasoning-effort",
            json={"reasoning_effort": "balanced"},
        )
        assert res.status_code == 200
        assert res.json()["reasoning_effort"] == "balanced"
        assert store.get_session(sid)["reasoning_effort"] == "balanced"

        bad = client.put(
            f"/api/sessions/{sid}/reasoning-effort",
            json={"reasoning_effort": "not-supported"},
        )
        assert bad.status_code == 400
    finally:
        _cleanup()
```

Also assert a logged-in user gets `404` when requesting or updating another user's session, matching the existing isolation test setup.

- [ ] **Step 2: Run the API tests and verify the expected failure**

Run:

```bash
pytest tests/integration/test_llm_profiles_api.py -k reasoning -v
```

Expected: FAIL with a missing route/schema error.

- [ ] **Step 3: Add the request schemas and routes**

Add `reasoning_efforts: list[str] = Field(default_factory=list, max_length=24)` to `LLMProfileCreate` and `reasoning_efforts: list[str] | None = Field(default=None, max_length=24)` to `LLMProfileUpdate`. Define `ReasoningEffortUpdate` with a bounded optional string and export all new schema types from `app/schemas/__init__.py`. Register the GET/PUT routes after session ownership is available:

```python
@router.get("/sessions/{session_id}/reasoning-effort")
async def get_session_reasoning_effort(session_id: str, request: Request, _: AuthDep):
    require_owned_session(request, session_id)
    state = store.get_session_reasoning_effort(session_id)
    if not state:
        raise HTTPException(status_code=404, detail="Session not found")
    return state


@router.put("/sessions/{session_id}/reasoning-effort")
async def set_session_reasoning_effort(
    session_id: str, body: ReasoningEffortUpdate, request: Request, _: AuthDep
):
    require_owned_session(request, session_id)
    try:
        state = store.set_session_reasoning_effort(session_id, body.reasoning_effort)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not state:
        raise HTTPException(status_code=404, detail="Session not found")
    return state
```

- [ ] **Step 4: Run the API tests and verify they pass**

Run:

```bash
pytest tests/integration/test_llm_profiles_api.py tests/integration/test_session_isolation_api.py -k reasoning -v
```

Expected: PASS, including the cross-user `404` behavior.

- [ ] **Step 5: Commit the API slice**

```bash
git add app/schemas/models.py app/schemas/__init__.py app/api/rest.py tests/integration/test_llm_profiles_api.py tests/integration/test_session_isolation_api.py
git commit -m "feat: add session reasoning effort API"
```

### Task 3: Forward reasoning effort through the runtime client

**Files:**
- Modify: `app/runtime/llm/openai_compat.py`
- Modify: `app/runtime/llm/__init__.py`
- Modify: `app/runtime/agent/loop.py`
- Test: `tests/unit/runtime/llm/test_openai_compat.py`
- Test: `tests/unit/runtime/llm/test_factory.py`
- Test: `tests/unit/runtime/agent/test_loop.py`

**Interfaces:**
- `OpenAICompatClient(..., reasoning_effort: str | None = None)` stores one trimmed optional value.
- `get_llm(agent_id: str | None = None, reasoning_effort: str | None = None) -> LLMClient` maps unsupported requested values to the selected profile's highest configured effort.
- `run_turn(..., reasoning_effort: str | None = None)` uses the effective session value when `session_id` is supplied and no explicit argument is passed.

- [ ] **Step 1: Write failing request and propagation tests**

Add request assertions for both client paths:

```python
async def test_reasoning_effort_is_forwarded_to_non_stream_request() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["reasoning_effort"] == "deep-thought"
        return httpx.Response(200, json=_completion_body(content="ok"))

    client = _client(httpx.MockTransport(handler), reasoning_effort="deep-thought")
    response = await client.complete([{"role": "user", "content": "hi"}])
    assert response.content == "ok"


async def test_reasoning_effort_is_forwarded_to_stream_request() -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return _completion_response(request, _completion_body(content="ok"))

    client = _client(httpx.MockTransport(handler), reasoning_effort="xhigh-provider")
    await client.complete([{"role": "user", "content": "hi"}], tools=[{"type": "function", "function": {"name": "noop"}}])
    assert seen["reasoning_effort"] == "xhigh-provider"
```

Add a factory test that a session's selected value reaches the constructed client, and a loop test that `run_turn(session_id=...)` asks the store for the effective session value before `get_llm`.

- [ ] **Step 2: Run focused runtime tests and verify the expected failure**

Run:

```bash
pytest tests/unit/runtime/llm/test_openai_compat.py tests/unit/runtime/llm/test_factory.py tests/unit/runtime/agent/test_loop.py -k reasoning -v
```

Expected: FAIL because the client payload and runtime signatures do not yet accept the effort.

- [ ] **Step 3: Add the optional payload field**

Store `self._reasoning_effort = (reasoning_effort or "").strip() or None`. Add the field to the dictionaries in both `complete()` and `stream_complete()` only when non-`None`:

```python
if self._reasoning_effort:
    payload["reasoning_effort"] = self._reasoning_effort
```

This keeps existing payloads byte-for-byte equivalent when no profile options are configured.

- [ ] **Step 4: Add profile-aware factory and loop resolution**

Update `get_llm()` to accept `reasoning_effort`, resolve the selected profile, and use `effective_reasoning_effort(profile, reasoning_effort)` before constructing `OpenAICompatClient`. In `run_turn()`, if the caller did not pass an explicit effort and has a `session_id`, call `store.resolve_session_reasoning_effort(session_id, agent_id)` and pass its result to `get_llm()`. Because nested delegation already passes the same `session_id`, direct, mention, and delegated paths all use the correct per-profile fallback.

- [ ] **Step 5: Run focused runtime tests and verify they pass**

Run:

```bash
pytest tests/unit/runtime/llm/test_openai_compat.py tests/unit/runtime/llm/test_factory.py tests/unit/runtime/agent/test_loop.py -k reasoning -v
```

Expected: PASS, followed by the full three test modules to catch regressions in ordinary requests.

- [ ] **Step 6: Commit the runtime slice**

```bash
git add app/runtime/llm/openai_compat.py app/runtime/llm/__init__.py app/runtime/agent/loop.py tests/unit/runtime/llm/test_openai_compat.py tests/unit/runtime/llm/test_factory.py tests/unit/runtime/agent/test_loop.py
git commit -m "feat: forward session reasoning effort to LLM requests"
```

### Task 4: Add profile editor and composer model/effort popover

**Files:**
- Modify: `app/templates/partials/settings/models.html`
- Modify: `app/static/js/system.js`
- Modify: `app/templates/partials/chat_composer.html`
- Modify: `app/static/css/tomo.css`
- Modify: `app/static/js/chat.js`
- Test: `tests/integration/test_llm_profiles_api.py`

**Interfaces:**
- Profile editor sends `reasoning_efforts: string[]` parsed from one trimmed line per value.
- Every shared composer contains `.composer-reasoning-trigger`, `.composer-reasoning-popover`, and `.composer-reasoning-flyout`; chat initialization owns their state and persistence handler.
- `refreshReasoningEffort()` fetches `/api/sessions/{id}/reasoning-effort`, renders the model/effort popover, and hides the control when no values are available.

- [ ] **Step 1: Write the failing page/API assertions**

Extend the existing profile/page sanity test to create a profile with `reasoning_efforts` and assert the API returns it. Add a rendered page assertion that `/system` contains `profReasoningEfforts` and `/sessions` contains `composer-reasoning-trigger`.

```python
assert body["reasoning_efforts"] == ["low", "provider-max"]
assert "profReasoningEfforts" in client.get("/system").text
assert "composer-reasoning-trigger" in client.get("/sessions").text
```

- [ ] **Step 2: Run the focused page tests and verify the expected failure**

Run:

```bash
pytest tests/integration/test_llm_profiles_api.py -k "profile_crud or pages_render" -v
```

Expected: FAIL because the templates do not contain the new controls.

- [ ] **Step 3: Add the profile textarea and client serialization**

Add a `textarea#profReasoningEfforts` with copy explaining one provider value per line. Populate it in `openForm()` from `p.reasoning_efforts.join("\\n")`; clear it for a new profile. In the save handler, include:

```javascript
function parseReasoningEfforts(value) {
  return String(value || '').split(/\r?\n/)
    .map(function (line) { return line.trim(); })
    .filter(Boolean);
}

body.reasoning_efforts = parseReasoningEfforts(fReasoning.value);
```

Update the profile row description with `reasoning_efforts.length + ' effort(s)'` so the per-model configuration is visible without opening edit.

- [ ] **Step 4: Add the composer model/effort popover markup and styling**

Insert this trigger in the left composer footer beside the permission mode:

```html
<div class="composer-reasoning hidden">
  <button class="composer-reasoning-trigger" type="button" aria-haspopup="true" aria-expanded="false">
    <span class="composer-reasoning-model">Model</span>
    <span class="composer-reasoning-effort">Reasoning</span>
    <span class="composer-reasoning-chevron" aria-hidden="true">⌄</span>
  </button>
  <div class="composer-reasoning-popover hidden" role="menu">
    <button class="composer-reasoning-row" type="button" data-reasoning-row="model">Model <span></span><b>›</b></button>
    <button class="composer-reasoning-row" type="button" data-reasoning-row="effort">Effort <span></span><b>›</b></button>
    <div class="composer-reasoning-divider"></div>
    <button class="composer-reasoning-reset" type="button">Reset to default <span>↻</span></button>
    <div class="composer-reasoning-flyout hidden" role="menu"></div>
  </div>
</div>
```

Style it like the supplied reference: a compact transparent footer trigger, rounded charcoal popover above the composer, row-level hover states, a right-side effort flyout, checkmark on the selected effort, and a muted reset row. Keep it usable while a turn is generating. Do not change the existing Send/Stop slot.

- [ ] **Step 5: Add session loading and immediate persistence in `chat.js`**

Add `paintReasoningEffort(payload)` to fill the model label, current effort label, effort row, and flyout options from `payload.model`, `payload.reasoning_efforts`, and `payload.reasoning_effort`; remove `hidden` when the profile has at least one value. The trigger opens/closes the popover, the effort row opens the flyout, and each flyout option saves immediately. Keep a one-option effort flyout visible when configured so the active model's default is explicit. When no `session_id` exists, hide the trigger/popover.

Add:

```javascript
async function refreshReasoningEffort() {
  var sid = currentSessionId();
  if (!sid || !reasoningSelect) {
    paintReasoningEffort(null);
    return;
  }
  try {
    var data = await Tomo.api('/api/sessions/' + encodeURIComponent(sid) + '/reasoning-effort');
    paintReasoningEffort(data);
  } catch (e) {
    paintReasoningEffort(null);
  }
}
```

On effort option click, PUT the selected value; on failure restore the prior labels and use `Tomo.toast`. The reset action sends a blank value so the server returns to the profile's last configured option. Call `refreshReasoningEffort()` beside `refreshApprovalMode()` during initialization and after `ensureSession()` creates a session. The existing session-page lifecycle destroys/reinitializes the chat handle on session changes, so each session reloads its own persisted value.

- [ ] **Step 6: Run page tests and inspect the UI assets**

Run:

```bash
pytest tests/integration/test_llm_profiles_api.py -k "profile_crud or pages_render" -v
```

Then use `rg` to verify all three hooks exist:

```bash
rg -n "profReasoningEfforts|composer-reasoning-trigger|composer-reasoning-popover|refreshReasoningEffort" app/templates app/static
```

Expected: PASS and exactly one implementation of each composer lifecycle hook.

- [ ] **Step 7: Commit the UI slice**

```bash
git add app/templates/partials/settings/models.html app/static/js/system.js app/templates/partials/chat_composer.html app/static/css/tomo.css app/static/js/chat.js tests/integration/test_llm_profiles_api.py
git commit -m "feat: add reasoning effort controls to profile and composer"
```

### Task 5: Full verification and cleanup

**Files:**
- Modify: any implementation/test files only if a failing regression requires a targeted fix.

- [ ] **Step 1: Run all focused feature tests**

```bash
pytest tests/unit/models/test_llm_profiles.py tests/unit/models/test_sessions_messages.py tests/integration/test_llm_profiles_api.py tests/integration/test_session_isolation_api.py tests/unit/runtime/llm/test_openai_compat.py tests/unit/runtime/llm/test_factory.py tests/unit/runtime/agent/test_loop.py -v
```

- [ ] **Step 2: Run the complete test suite and compiler/lint checks**

```bash
pytest -q
python -m compileall -q app
ruff check app tests
```

Expected: all commands exit successfully. If a pre-existing lint warning is present, report its exact file and line rather than changing unrelated code.

- [ ] **Step 3: Inspect the final diff and working tree**

```bash
git diff HEAD~4..HEAD --stat
git status --short
git diff --check HEAD~4..HEAD
```

Confirm the only unstaged change is the user's pre-existing `.gitignore` modification, and that no API key or secret appears in the diff.

- [ ] **Step 4: Commit any targeted verification fix**

```bash
git add app/models/schema.py app/models/mixins/llm_profiles.py app/models/mixins/sessions.py app/services/store.py app/schemas/models.py app/schemas/__init__.py app/api/rest.py app/runtime/llm/openai_compat.py app/runtime/llm/__init__.py app/runtime/agent/loop.py app/templates/partials/settings/models.html app/static/js/system.js app/templates/partials/chat_composer.html app/static/css/tomo.css app/static/js/chat.js tests/unit tests/integration
git commit -m "fix: address reasoning effort verification regression"
```

Run this only when a targeted verification fix was needed; do not stage `.gitignore`.
