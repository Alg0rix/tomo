# LLM Session Auto-Title Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** After the first assistant reply, replace the provisional session title with a short LLM-generated name (once per session), without blocking streamed deltas.

**Architecture:** Keep first-message `derive_session_title` as a provisional SSE update. After the agent loop finishes on an eligible first turn, call `generate_session_title` via settings `get_llm().complete()`, sanitize, `set_session_title`, emit a second `session` SSE. Failures keep the provisional title.

**Tech Stack:** FastAPI SSE, SQLite sessions, OpenAI-compat / MockLLMClient, pytest.

## Global Constraints

- Use settings-backed `get_llm()` only (no mock provider in product path).
- Never fail the chat turn because title generation failed.
- LLM title at most once; do not overwrite after title no longer matches provisional derived text.
- UI already handles `session` SSE — no frontend changes required unless broken.
- Do not commit unless the user asks.

---

### Task 1: Title helpers (sanitize + generate + eligibility)

**Files:**
- Create: `app/runtime/session_title.py`
- Test: `tests/unit/runtime/test_session_title.py`

**Interfaces:**
- Produces:
  - `sanitize_llm_title(raw: str, *, max_len: int = 60) -> str | None`
  - `async def generate_session_title(user_text: str, assistant_text: str, *, llm: LLMClient | None = None) -> str | None`
  - `def should_llm_title(session: dict[str, Any] | None, history: list[dict[str, Any]]) -> bool`
  - `def first_user_and_final(history: list[dict[str, Any]]) -> tuple[str, str] | None`

- [ ] **Step 1: Write failing unit tests**

```python
import pytest
from app.runtime.session_title import (
    sanitize_llm_title,
    should_llm_title,
    first_user_and_final,
    generate_session_title,
)
from app.runtime.llm.base import LLMResponse

def test_sanitize_strips_quotes_and_truncates():
    assert sanitize_llm_title('  "Q3 Launch Plan"  ') == "Q3 Launch Plan"
    assert sanitize_llm_title("") is None
    assert sanitize_llm_title("x" * 80).endswith("…")

def test_should_llm_title_only_first_completed_turn():
    hist = [
        {"type": "user", "content": "Plan the Q3 launch carefully"},
        {"type": "final", "content": "Here is a plan..."},
    ]
    s = {"title": "Plan the Q3 launch carefully"}
    assert should_llm_title(s, hist) is True
    assert should_llm_title({"title": "Q3 Launch Plan"}, hist) is False
    assert should_llm_title(s, hist + [{"type": "user", "content": "more"}]) is False

@pytest.mark.asyncio
async def test_generate_uses_llm_and_sanitizes():
    class _L:
        async def complete(self, messages, tools=None):
            return LLMResponse(content=' "Billing Follow-up" ', tool_calls=[])
    title = await generate_session_title("help with invoice", "sure", llm=_L())
    assert title == "Billing Follow-up"
```

- [ ] **Step 2: Implement `app/runtime/session_title.py`**

System prompt: short title only, 3–6 words, no quotes/markdown. Truncate user/assistant inputs to 500 chars. On `LLMConfigError` / any exception / empty sanitize → return `None`. `should_llm_title`: session non-None; exactly one `user` entry; at least one `final`; `session["title"] == derive_session_title(first_user_content)`.

- [ ] **Step 3: Run tests — expect PASS**

Run: `uv run pytest tests/unit/runtime/test_session_title.py -q`

---

### Task 2: `set_session_title` on store

**Files:**
- Modify: `app/models/mixins/sessions.py`
- Modify: `app/services/store.py`
- Test: `tests/unit/models/test_sessions_messages.py`

**Interfaces:**
- Produces: `sessions_store.set_session_title(conn, session_id, title) -> dict | None`
- Produces: `store.set_session_title(session_id, title) -> dict | None`

- [ ] **Step 1: Failing test** — `set_session_title` updates title and `updated_at`.
- [ ] **Step 2: Implement** UPDATE sessions SET title=?, updated_at=? WHERE id=?
- [ ] **Step 3: pytest pass**

---

### Task 3: Wire into `stream_turn_sse`

**Files:**
- Modify: `app/channels/web.py` (after agent loop, before trailing busy-false)
- Test: `tests/integration/test_chat_mock.py`

**Interfaces:**
- Consumes: `should_llm_title`, `first_user_and_final`, `generate_session_title`, `store.set_session_title`

- [ ] **Step 1: Integration test** — monkeypatch `app.runtime.session_title.get_llm` (or `generate_session_title`'s default) so first turn yields provisional `session` then LLM `session`; second turn yields no `session`.

```python
async def test_llm_upgrades_session_title_after_first_final(tmp_path, monkeypatch):
    store.rebind(tmp_path / "title_llm.db")
    sid = store.create_swarm_session(["main"], user_id="web")

    class _TitleLLM:
        async def complete(self, messages, tools=None):
            from app.runtime.llm.base import LLMResponse
            return LLMResponse(content="Greeting Chat", tool_calls=[])
        async def stream_complete(self, messages, tools=None):
            # unused if only title path uses complete; agent loop uses get_llm in loop
            ...
```

Better approach for integration: monkeypatch only `generate_session_title` to return `"Greeting Chat"`, keep MockLLM for the agent loop:

```python
async def fake_gen(user, asst, *, llm=None):
    return "Greeting Chat"
monkeypatch.setattr("app.channels.web.generate_session_title", fake_gen)
```

Assert: `_data(events, "session")` titles `== ["hello there", "Greeting Chat"]` for message `"hello there"`. Second turn: no session events; title stays `"Greeting Chat"`.

- [ ] **Step 2: Implement wiring in `web.py`** after the try/finally agent section, before busy-false yield: if agent existed and loop ran, check eligibility, await generate, set title, yield session SSE.
- [ ] **Step 3: Failure test** — `generate_session_title` returns `None` → only provisional session event; history still has final.
- [ ] **Step 4: Full related pytest green**

---

### Task 4: Spec status + smoke

- [ ] Mark design doc status `accepted`.
- [ ] Restart Tomo if needed; hard-refresh `/sessions`.

## Spec coverage

| Spec item | Task |
|---|---|
| Provisional first-message title + SSE | already exists; Task 3 keeps it |
| LLM after first final | Task 1 + 3 |
| Sanitize / empty keep provisional | Task 1 |
| Failure soft | Task 1 + 3 |
| Once per session | Task 1 `should_llm_title` |
| No UI change | N/A |
| Out of scope (manual rename, etc.) | skipped |
