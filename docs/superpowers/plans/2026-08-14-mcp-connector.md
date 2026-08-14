# MCP Connector Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add configurable local `stdio` and remote Streamable HTTP MCP servers to Tomo, with encrypted credentials, capability discovery, global and per-agent tool controls, and usable tools/resources/prompts in the UI and agent runtime.

**Architecture:** Persist MCP server configuration and discovered capability snapshots in SQLite. Keep live MCP SDK sessions in a process-local async connection manager, expose connected MCP tools through a namespaced catalog merged with Tomo's per-agent tool catalog, and route calls through an async registry adapter into the existing permission/event loop. Add a dedicated System → MCP UI plus resource/prompt actions in chat.

**Tech Stack:** Python 3.12, FastAPI, SQLite, official Python MCP SDK (`mcp>=1.27,<2`), existing `httpx`, Pydantic, Jinja2, vanilla JavaScript, pytest/pytest-asyncio, Ruff.

## Global Constraints

- Support only MCP `stdio` and Streamable HTTP transports; do not add legacy HTTP+SSE, OAuth, sampling, elicitation, roots, subscriptions, or server-initiated client requests.
- Launch `stdio` servers with an argument vector and `shell=False`; never interpolate a command into a shell string.
- Encrypt every configured `stdio` environment value and HTTP header value with the existing Fernet helpers; never return or log plaintext secrets.
- Use namespaced MCP tool IDs in the form `mcp__<server>__<tool>`, normalized and deterministically shortened when needed.
- Keep server/item enablement checks in both schema construction and execution dispatch.
- Keep cached discovery visible for repair, but advertise MCP tools only after the pre-turn reconnect step confirms the server is live.
- Treat MCP metadata, annotations, instructions, resource content, and prompt content as untrusted input.
- Preserve the existing built-in tool registry, approval modes, SSE events, and per-agent `agent_tools` behavior.
- Use local fake MCP servers or injected transports in tests; no test may call an external MCP endpoint.
- Do not stage or modify the pre-existing `.gitignore` change.

---

## File map

Create these focused units:

- `app/models/mixins/mcp.py` — SQLite CRUD, secret masking, discovery snapshot replacement, and capability rows.
- `app/runtime/mcp/__init__.py` — public manager and helper exports.
- `app/runtime/mcp/names.py` — stable server/runtime ID normalization and MCP tool ID mapping.
- `app/runtime/mcp/discovery.py` — pagination and MCP result-to-catalog normalization.
- `app/runtime/mcp/results.py` — bounded conversion of MCP tool/resource/prompt content to Tomo-safe values.
- `app/runtime/mcp/manager.py` — SDK transport/session lifecycle, reconnect, discovery, and live calls.
- `app/templates/partials/settings/mcp.html` — System → MCP markup.
- `app/static/js/mcp.js` — MCP server form, capability rows, refresh/toggle/delete interactions, and resource/prompt actions.
- `tests/unit/models/test_mcp.py` — persistence and masking tests.
- `tests/unit/runtime/mcp/test_names.py` — stable ID tests.
- `tests/unit/runtime/mcp/test_discovery.py` — pagination/catalog normalization tests.
- `tests/unit/runtime/mcp/test_manager.py` — manager lifecycle and call tests with injected fake sessions.
- `tests/unit/runtime/tools/test_mcp_catalog.py` — merged schemas and enablement tests.
- `tests/integration/test_mcp_api.py` — authenticated API CRUD/discovery/resource/prompt tests.

Modify these existing seams:

- `pyproject.toml`, `uv.lock` — add and lock the official SDK.
- `app/models/schema.py`, `tests/unit/models/test_schema.py` — add MCP tables/indexes.
- `app/services/store.py` — facade methods and merged agent tool catalogs.
- `app/runtime/tools/registry.py` — async MCP dispatch while preserving sync built-ins.
- `app/runtime/permissions/types.py`, `assess.py`, `gate.py` — external MCP approval finding.
- `app/runtime/agent/loop.py`, `app/runtime/agent/atg/executor.py` — async dispatch and pre-turn reconnect.
- `app/api/platform.py`, `app/schemas/models.py`, `app/schemas/__init__.py` — API schemas and routes.
- `app/main.py` — manager shutdown/startup lifecycle hook.
- `app/web/pages.py`, `app/templates/system.html`, `app/templates/partials/settings/nav.html`, `app/static/js/system.js` — System page wiring and hash navigation.
- `app/templates/partials/agent_studio/panel_tools.html`, `app/static/js/agent_detail.js` only if the dynamic source/disabled presentation needs hooks beyond existing selectors.
- `app/templates/partials/chat_composer.html`, `app/static/js/chat.js`, `app/static/css/tomo.css` — MCP prompt/resource composer actions.
- `tests/integration/test_llm_profiles_api.py` or a focused page test — System MCP section render assertion if no new page test is preferable.

---

### Task 1: Add the official MCP SDK dependency

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`

**Interfaces:**
- Produces the importable `mcp` package used by Tasks 4–7.

- [ ] **Step 1: Add the stable major constraint**

Add this dependency to the existing `project.dependencies` list:

```toml
"mcp>=1.27,<2",
```

Keep the upper bound so the implementation uses the documented v1 client API
until a deliberate v2 migration is made.

- [ ] **Step 2: Resolve the lockfile**

Run:

```bash
uv lock
```

Expected: `uv.lock` changes only for `mcp` and its transitive dependencies.

- [ ] **Step 3: Verify imports**

Run:

```bash
uv run python -c "from mcp import ClientSession, StdioServerParameters; from mcp.client.stdio import stdio_client; from mcp.client.streamable_http import streamable_http_client"
```

Expected: command exits 0.

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "build: add MCP client SDK"
```

---

### Task 2: Add the MCP SQLite schema

**Files:**
- Modify: `app/models/schema.py` in `_SCHEMA` and `migrate()`
- Modify: `tests/unit/models/test_schema.py`

**Interfaces:**
- Produces `mcp_servers` and `mcp_items` tables consumed by `app/models/mixins/mcp.py`.

- [ ] **Step 1: Write migration assertions**

Extend `EXPECTED_TABLES` with `mcp_servers` and `mcp_items`, then add a test that
checks the required columns:

```python
def test_mcp_tables_have_runtime_columns(tmp_path):
    conn = sqlite3.connect(tmp_path / "mcp.db")
    migrate(conn)
    server_cols = {row[1] for row in conn.execute("PRAGMA table_info(mcp_servers)")}
    item_cols = {row[1] for row in conn.execute("PRAGMA table_info(mcp_items)")}
    assert {"id", "name", "transport", "command", "args_json", "url", "env_ciphertext", "headers_ciphertext", "enabled", "status", "status_message", "server_info_json", "capabilities_json", "last_connected_at", "last_discovered_at", "created_at", "updated_at"} <= server_cols
    assert {"id", "server_id", "kind", "runtime_id", "name", "title", "description", "uri", "mime_type", "schema_json", "metadata_json", "enabled", "created_at", "updated_at"} <= item_cols
```

- [ ] **Step 2: Run the schema tests to verify failure**

Run:

```bash
uv run pytest tests/unit/models/test_schema.py -q
```

Expected: the new table/column assertions fail before the DDL exists.

- [ ] **Step 3: Add idempotent DDL**

Add these tables to `_SCHEMA`:

```sql
CREATE TABLE IF NOT EXISTS mcp_servers (
    id                  TEXT PRIMARY KEY,
    name                TEXT NOT NULL,
    transport           TEXT NOT NULL,
    command             TEXT NOT NULL DEFAULT '',
    args_json           TEXT NOT NULL DEFAULT '[]',
    url                 TEXT NOT NULL DEFAULT '',
    env_ciphertext      TEXT NOT NULL DEFAULT '',
    headers_ciphertext  TEXT NOT NULL DEFAULT '',
    enabled             INTEGER NOT NULL DEFAULT 1,
    status              TEXT NOT NULL DEFAULT 'unknown',
    status_message      TEXT NOT NULL DEFAULT '',
    server_info_json    TEXT NOT NULL DEFAULT '{}',
    capabilities_json   TEXT NOT NULL DEFAULT '{}',
    last_connected_at   REAL NOT NULL DEFAULT 0,
    last_discovered_at  REAL NOT NULL DEFAULT 0,
    created_at          REAL NOT NULL DEFAULT 0,
    updated_at          REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS mcp_items (
    id             TEXT PRIMARY KEY,
    server_id      TEXT NOT NULL REFERENCES mcp_servers(id) ON DELETE CASCADE,
    kind           TEXT NOT NULL,
    runtime_id     TEXT NOT NULL DEFAULT '',
    name           TEXT NOT NULL DEFAULT '',
    title          TEXT NOT NULL DEFAULT '',
    description    TEXT NOT NULL DEFAULT '',
    uri            TEXT NOT NULL DEFAULT '',
    mime_type      TEXT NOT NULL DEFAULT '',
    schema_json    TEXT NOT NULL DEFAULT '{}',
    metadata_json  TEXT NOT NULL DEFAULT '{}',
    enabled        INTEGER NOT NULL DEFAULT 1,
    created_at     REAL NOT NULL DEFAULT 0,
    updated_at     REAL NOT NULL DEFAULT 0,
    UNIQUE (server_id, kind, name, uri)
);

CREATE INDEX IF NOT EXISTS idx_mcp_items_server_kind
    ON mcp_items(server_id, kind, enabled);
CREATE UNIQUE INDEX IF NOT EXISTS idx_mcp_items_runtime_id
    ON mcp_items(runtime_id) WHERE runtime_id <> '';
```

Keep foreign keys enforced through the existing connection helper.

- [ ] **Step 4: Run migration tests**

Run:

```bash
uv run pytest tests/unit/models/test_schema.py -q
```

Expected: PASS, including the existing idempotency test.

- [ ] **Step 5: Commit**

```bash
git add app/models/schema.py tests/unit/models/test_schema.py
git commit -m "feat: add MCP persistence tables"
```

---

### Task 3: Implement MCP persistence and secret-safe views

**Files:**
- Create: `app/models/mixins/mcp.py`
- Modify: `app/services/store.py`
- Test: `tests/unit/models/test_mcp.py`

**Interfaces:**
- Consumes: `mcp_servers` and `mcp_items` from Task 2; `encrypt_secret`, `decrypt_secret` from `app/core/secrets.py`.
- Produces store methods:
  - `Store.list_mcp_servers() -> list[dict[str, Any]]`
  - `Store.get_mcp_server(server_id: str, *, include_secrets: bool = False) -> dict[str, Any] | None`
  - `Store.create_mcp_server(data: dict[str, Any]) -> dict[str, Any]`
  - `Store.update_mcp_server(server_id: str, data: dict[str, Any]) -> dict[str, Any] | None`
  - `Store.delete_mcp_server(server_id: str) -> bool`
  - `Store.list_mcp_items(server_id: str, *, kind: str | None = None, enabled_only: bool = False) -> list[dict[str, Any]]`
  - `Store.replace_mcp_items(server_id: str, items: list[dict[str, Any]]) -> list[dict[str, Any]]`
  - `Store.set_mcp_item_enabled(item_id: str, enabled: bool) -> dict[str, Any] | None`
  - `Store.set_mcp_status(server_id: str, status: str, message: str = "", *, connected_at: float | None = None, discovered_at: float | None = None) -> dict[str, Any] | None`
  - `Store.reset_mcp_runtime_statuses() -> None`

- [ ] **Step 1: Write persistence tests**

Cover creation, update, delete cascade, item replacement, item toggle, and
public masking. Include an at-rest assertion:

```python
def test_mcp_secrets_are_ciphertext_and_public_views_are_masked(tmp_path):
    store.rebind(tmp_path / "mcp.db")
    created = store.create_mcp_server({
        "id": "github",
        "name": "GitHub",
        "transport": "streamable_http",
        "url": "https://mcp.example/mcp",
        "headers": {"Authorization": "Bearer secret-token"},
    })
    assert created["headers_keys"] == ["Authorization"]
    assert created["headers_set"] is True
    assert "secret-token" not in str(created)
    raw = store._conn.execute("SELECT headers_ciphertext FROM mcp_servers WHERE id='github'").fetchone()[0]
    assert raw.startswith("enc:v1:")
    assert "secret-token" not in raw
```

Also verify a missing/blank secret map preserves ciphertext on update and that
`include_secrets=True` is only used internally.

- [ ] **Step 2: Run the new tests to verify failure**

Run:

```bash
uv run pytest tests/unit/models/test_mcp.py -q
```

Expected: FAIL because the mixin and store methods do not exist.

- [ ] **Step 3: Add safe conversion helpers**

In `app/models/mixins/mcp.py`, implement `_public_server(row)` so public rows
contain `env_keys`, `headers_keys`, `env_set`, and `headers_set`, but not raw
secret maps. Store JSON metadata as decoded dicts/lists and booleans as Python
`bool` values, matching other mixins.

Implement validation before writes:

```python
_TRANSPORTS = {"stdio", "streamable_http"}
_KINDS = {"tool", "resource", "resource_template", "prompt"}
```

Require `command` for `stdio`, `url` for `streamable_http`, and keep `args` a
list of strings. Use `encrypt_secret(json.dumps(mapping, sort_keys=True))` for
the two secret maps. Omitted maps preserve existing ciphertext; supplied maps
replace them, and a masked value `••••` preserves an existing same-key value.

- [ ] **Step 4: Add store facade wrappers**

Import the mixin as `mcp_store`, guard every call with the existing `RLock`, and
reset runtime statuses to `unknown` in `_open()` after migration so a process
restart never trusts a persisted `connected` status.

- [ ] **Step 5: Run persistence tests**

Run:

```bash
uv run pytest tests/unit/models/test_mcp.py tests/unit/models/test_schema.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/models/mixins/mcp.py app/services/store.py tests/unit/models/test_mcp.py
git commit -m "feat: persist MCP servers and capabilities"
```

---

### Task 4: Build stable MCP IDs and discovery normalization

**Files:**
- Create: `app/runtime/mcp/__init__.py`
- Create: `app/runtime/mcp/names.py`
- Create: `app/runtime/mcp/discovery.py`
- Create: `app/runtime/mcp/results.py`
- Test: `tests/unit/runtime/mcp/test_names.py`
- Test: `tests/unit/runtime/mcp/test_discovery.py`

**Interfaces:**
- Produces:
  - `runtime_tool_id(server_id: str, tool_name: str) -> str`
  - `split_runtime_tool_id(runtime_id: str) -> tuple[str, str] | None`
  - `paginate_mcp_list(fetch_page: Callable[[str | None], Awaitable[Any]], field: str) -> list[Any]`
  - `normalize_tool(server: dict[str, Any], raw_tool: Any) -> dict[str, Any]`
  - `normalize_resource(server: dict[str, Any], raw_resource: Any) -> dict[str, Any]`
  - `normalize_prompt(server: dict[str, Any], raw_prompt: Any) -> dict[str, Any]`
  - `render_tool_result(result: Any, *, max_chars: int) -> str`
  - `render_resource_result(result: Any, *, max_chars: int) -> dict[str, Any]`
  - `render_prompt_result(result: Any, *, max_chars: int) -> dict[str, Any]`

- [ ] **Step 1: Write ID and pagination tests**

Assert stable normalization, safe characters, round-tripping, deterministic
shortening, and opaque cursor handling:

```python
def test_runtime_tool_id_round_trips():
    runtime_id = runtime_tool_id("github", "create_issue")
    assert runtime_id == "mcp__github__create_issue"
    assert split_runtime_tool_id(runtime_id) == ("github", "create_issue")


@pytest.mark.asyncio
async def test_pagination_follows_opaque_cursors():
    cursors = []
    pages = {None: ([1], "next-a"), "next-a": ([2], "next-b"), "next-b": ([3], None)}

    async def fetch(cursor):
        cursors.append(cursor)
        values, next_cursor = pages[cursor]
        return {"items": values, "nextCursor": next_cursor}

    assert await paginate_mcp_list(fetch, "items") == [1, 2, 3]
    assert cursors == [None, "next-a", "next-b"]
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
uv run pytest tests/unit/runtime/mcp/test_names.py tests/unit/runtime/mcp/test_discovery.py -q
```

Expected: FAIL because the new modules are absent.

- [ ] **Step 3: Implement ID/schema normalization**

Normalize tool input schemas to an OpenAI function schema:

```python
{
    "type": "function",
    "function": {
        "name": runtime_id,
        "description": title_or_description_or_original_name,
        "parameters": input_schema_or_empty_object_schema,
    },
}
```

Store the original MCP name, server ID, output schema, annotations, and raw
metadata separately in `metadata_json`; do not trust annotations for safety.
Normalize resource URI/mime type and prompt argument definitions without
executing content.

Implement `paginate_mcp_list` using only the server-provided opaque
`nextCursor`; stop on a missing or empty cursor and cap the total number of
items at 10,000 per capability family.

- [ ] **Step 4: Implement bounded result rendering**

Render text blocks and structured content as readable JSON/text. Render
resource links with their URI and label. For image/audio/blob blocks, return a
bounded type/mime/size summary rather than embedding arbitrary base64 in the
agent context. Apply `max_chars` after rendering.

- [ ] **Step 5: Run focused tests**

Run:

```bash
uv run pytest tests/unit/runtime/mcp/test_names.py tests/unit/runtime/mcp/test_discovery.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/runtime/mcp tests/unit/runtime/mcp/test_names.py tests/unit/runtime/mcp/test_discovery.py
git commit -m "feat: normalize MCP capability catalogs"
```

---

### Task 5: Implement the async MCP connection manager

**Files:**
- Create: `app/runtime/mcp/manager.py`
- Modify: `app/runtime/mcp/__init__.py`
- Test: `tests/unit/runtime/mcp/test_manager.py`

**Interfaces:**
- Consumes: Task 3 store methods, Task 4 discovery/result helpers, and official SDK transports.
- Produces:
  - `class McpConnectionManager`
  - `mcp_manager: McpConnectionManager`
  - `async McpConnectionManager.connect_and_discover(server_id: str) -> dict[str, Any]`
  - `async McpConnectionManager.ensure_for_servers(server_ids: set[str]) -> set[str]`
  - `async McpConnectionManager.call_tool(runtime_id: str, arguments: dict[str, Any]) -> str`
  - `async McpConnectionManager.read_resource(server_id: str, uri: str) -> dict[str, Any]`
  - `async McpConnectionManager.get_prompt(server_id: str, name: str, arguments: dict[str, str] | None = None) -> dict[str, Any]`
  - `async McpConnectionManager.close_server(server_id: str) -> None`
  - `async McpConnectionManager.close_all() -> None`
  - `McpConnectionManager.connected_server_ids() -> set[str]`

- [ ] **Step 1: Write fake-session tests**

Create a `FakeSession` in `tests/unit/runtime/mcp/test_manager.py` with
`initialize`, `list_tools`, `list_resources`, `list_resource_templates`,
`list_prompts`, `call_tool`, `read_resource`, and `get_prompt` async methods.
Test that `connect_and_discover` persists all four capability families, that a
failed session sets `error`, and that a second call reuses the live session.

- [ ] **Step 2: Run manager tests to verify failure**

Run:

```bash
uv run pytest tests/unit/runtime/mcp/test_manager.py -q
```

Expected: FAIL because the manager is absent.

- [ ] **Step 3: Add session/transport ownership**

Represent each live connection with an internal record containing the server
ID, `AsyncExitStack`, `ClientSession`, and an `asyncio.Lock`. On `connect`:

```python
params = StdioServerParameters(command=command, args=args, env=env)
transport = stdio_client(params)
```

For HTTP, create an `httpx.AsyncClient(headers=headers, follow_redirects=True)`
and pass it to `streamable_http_client(url, http_client=client)`. Enter the
transport and `ClientSession`, call `initialize()`, then discover each
capability list with Task 4 pagination.

Keep transport creation behind injectable factories so tests can provide the
fake session without starting subprocesses or network listeners.

- [ ] **Step 4: Implement discovery persistence and reconnect**

Call `store.replace_mcp_items()` only after all discovery calls succeed. Store
server info/capabilities and timestamps, then mark `connected`. On failure,
close partial resources, keep the old item snapshot for the UI, mark `error`
with a sanitized bounded message, and do not expose the server as connected.

`ensure_for_servers` connects each requested enabled server, returns only the
IDs with live sessions, and retries a closed session once. `call_tool`,
`read_resource`, and `get_prompt` each acquire the server lock and call
`ensure_for_servers` for the target before dispatch.

- [ ] **Step 5: Add shutdown tests and run focused tests**

Assert `close_all()` exits every fake session exactly once and that reconnect
after a simulated closed session calls the transport factory again.

Run:

```bash
uv run pytest tests/unit/runtime/mcp/test_manager.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/runtime/mcp/manager.py app/runtime/mcp/__init__.py tests/unit/runtime/mcp/test_manager.py
git commit -m "feat: add MCP connection manager"
```

---

### Task 6: Merge MCP catalogs into Store and add async registry dispatch

**Files:**
- Modify: `app/services/store.py`
- Modify: `app/runtime/tools/registry.py`
- Create: `tests/unit/runtime/tools/test_mcp_catalog.py`

**Interfaces:**
- Consumes: Task 3 persistence, Task 4 schema normalization, Task 5 `mcp_manager`.
- Produces:
  - `Store.list_mcp_tool_catalog(*, connected_server_ids: set[str] | None = None) -> list[dict[str, Any]]`
  - `Store.list_mcp_server_ids_for_agent(agent_id: str) -> set[str]`
  - `Store.get_agent_tools(agent_id: str) -> list[dict[str, Any]]` with built-in and MCP rows
  - `Store.get_agent_openai_tools(agent_id: str, *, connected_server_ids: set[str] | None = None) -> list[dict[str, Any]]`
  - `async execute_async(name: str, arguments: dict[str, Any]) -> str`
  - `is_mcp_tool_name(name: str) -> bool`

- [ ] **Step 1: Write catalog tests**

Insert a connected enabled server and two tool items directly through the
store, then assert:

- `get_agent_tools("main")` includes `mcp__server__tool` with `backend="mcp:server"`.
- A disabled global item is present in the UI catalog but not in connected
  OpenAI schemas.
- An explicit `agent_tools` false row removes only that agent's MCP tool.
- A disabled server removes all of its MCP tools from agent schemas.

- [ ] **Step 2: Run catalog tests to verify failure**

Run:

```bash
uv run pytest tests/unit/runtime/tools/test_mcp_catalog.py -q
```

Expected: FAIL because Store does not merge MCP rows.

- [ ] **Step 3: Add Store catalog methods**

Keep `Store.list_tools()` as the built-in registry catalog for existing System
Tools behavior. Add MCP catalog rows with:

```python
{
    "id": item["runtime_id"],
    "name": item["title"] or item["name"],
    "description": item["description"],
    "backend": f"mcp:{item['server_id']}",
    "server_id": item["server_id"],
    "mcp_name": item["name"],
    "enabled": bool(item["enabled"] and server["enabled"]),
    "locked": not bool(item["enabled"] and server["enabled"]),
}
```

Use `agent_tools` for per-agent MCP tool enablement exactly as for built-ins;
artifact locks remain unchanged.

- [ ] **Step 4: Add async registry dispatch**

Implement:

```python
async def execute_async(name: str, arguments: dict[str, Any]) -> str:
    if is_mcp_tool_name(name):
        from app.runtime.mcp import mcp_manager
        return await mcp_manager.call_tool(name, arguments)
    return await asyncio.to_thread(execute, name, arguments)
```

Before `call_tool`, the manager rechecks the server/item rows and returns a
bounded error for unknown, disabled, or disconnected capability IDs.

- [ ] **Step 5: Run catalog and existing registry tests**

Run:

```bash
uv run pytest tests/unit/runtime/tools/test_mcp_catalog.py tests/unit/runtime/tools/test_registry.py tests/unit/runtime/tools/test_agent_tools.py -q
```

Expected: PASS with all existing built-in assertions unchanged.

- [ ] **Step 6: Commit**

```bash
git add app/services/store.py app/runtime/tools/registry.py tests/unit/runtime/tools/test_mcp_catalog.py
git commit -m "feat: merge MCP tools into agent catalogs"
```

---

### Task 7: Add MCP permission findings and async agent execution

**Files:**
- Modify: `app/runtime/permissions/types.py`
- Modify: `app/runtime/permissions/assess.py`
- Modify: `app/runtime/permissions/gate.py`
- Modify: `app/runtime/agent/loop.py`
- Modify: `app/runtime/agent/atg/executor.py`
- Test: `tests/integration/test_permissions_chat.py`
- Test: `tests/unit/runtime/agent/test_mcp_execution.py`

**Interfaces:**
- Consumes: Task 6 `execute_async`, Task 5 `ensure_for_servers`.
- Produces an `external` permission finding for every `mcp__…` call and uses
  the async dispatcher in all agent execution paths.

- [ ] **Step 1: Write permission/execution tests**

Add tests that assert:

```python
assessment = assess("mcp__github__create_issue", {"title": "x"}, tmp_path)
assert any(f.kind == "external" for f in assessment.findings)
```

Add an async test that monkeypatches `registry.execute_async`, calls
`_execute_authorized`, and verifies the MCP function receives the original
arguments without using `asyncio.to_thread` for the MCP path. Keep existing
built-in approval tests unchanged.

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
uv run pytest tests/integration/test_permissions_chat.py tests/unit/runtime/agent/test_mcp_execution.py -q
```

Expected: the new external-finding and async-dispatch assertions fail.

- [ ] **Step 3: Add the external finding kind**

Extend `FindingKind` with `"external"`. In `assess()`, append:

```python
if tool.startswith("mcp__"):
    findings.append(Finding(
        kind="external",
        key=f"mcp:{tool}",
        description=f"external MCP tool {tool}",
    ))
```

Keep user-deny matching and hardline handling intact. Include `external` in
approval allowlist keys so users can choose once/session/always under the
existing UI; never use MCP annotations to change the finding.

- [ ] **Step 4: Switch runtime paths to `execute_async`**

In `app/runtime/agent/loop.py`, import `execute_async` and replace the
worker-thread call inside `_execute_authorized`. Replace the delegate bundle's
direct `asyncio.to_thread(execute, …)` call too. In
`app/runtime/agent/atg/executor.py`, replace its worker-thread dispatch with
`await execute_async(tool, args)`.

Before `store.get_agent_openai_tools(agent_id)`, await the manager's
pre-turn reconnect:

```python
from app.runtime.mcp import mcp_manager

connected = await mcp_manager.ensure_for_servers(
    store.list_mcp_server_ids_for_agent(agent_id)
)
tool_schemas = store.get_agent_openai_tools(
    agent_id, connected_server_ids=connected
)
```

Do not alter the injected `tools=` test override path. Background learning
continues to use its explicit built-in allowlist.

- [ ] **Step 5: Run focused and chat tests**

Run:

```bash
uv run pytest tests/integration/test_permissions_chat.py tests/unit/runtime/agent/test_mcp_execution.py tests/integration/test_chat_mock.py tests/integration/test_chat_sse.py -q
```

Expected: PASS; built-in tool events and approval behavior remain unchanged.

- [ ] **Step 6: Commit**

```bash
git add app/runtime/permissions app/runtime/agent/loop.py app/runtime/agent/atg/executor.py tests/integration/test_permissions_chat.py tests/unit/runtime/agent/test_mcp_execution.py
git commit -m "feat: route MCP calls through agent approvals"
```

---

### Task 8: Add MCP API schemas, routes, and lifespan cleanup

**Files:**
- Create: `app/schemas/mcp.py`
- Modify: `app/schemas/__init__.py`
- Modify: `app/api/platform.py`
- Modify: `app/main.py`
- Test: `tests/integration/test_mcp_api.py`

**Interfaces:**
- Consumes: Tasks 3–7 store/manager/catalog interfaces.
- Produces authenticated API routes:
  - `GET/POST /api/mcp-servers`
  - `GET/PUT/DELETE /api/mcp-servers/{server_id}`
  - `POST /api/mcp-servers/{server_id}/refresh`
  - `PUT /api/mcp-servers/{server_id}/items/{item_id}`
  - `GET /api/mcp-servers/{server_id}/resources`
  - `POST /api/mcp-servers/{server_id}/resources/read`
  - `GET /api/mcp-servers/{server_id}/prompts`
  - `POST /api/mcp-servers/{server_id}/prompts/get`

- [ ] **Step 1: Define request models**

In `app/schemas/mcp.py`, add:

```python
class McpServerCreate(BaseModel):
    id: str | None = Field(default=None, min_length=2, max_length=64, pattern=r"^[a-z0-9_]+$")
    name: str = Field(min_length=1, max_length=80)
    transport: Literal["stdio", "streamable_http"]
    command: str = Field(default="", max_length=400)
    args: list[str] = Field(default_factory=list, max_length=64)
    url: str = Field(default="", max_length=2000)
    env: dict[str, str] | None = None
    headers: dict[str, str] | None = None
    enabled: bool = True


class McpServerUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    transport: Literal["stdio", "streamable_http"] | None = None
    command: str | None = Field(default=None, max_length=400)
    args: list[str] | None = Field(default=None, max_length=64)
    url: str | None = Field(default=None, max_length=2000)
    env: dict[str, str] | None = None
    headers: dict[str, str] | None = None
    enabled: bool | None = None


class McpItemEnabled(BaseModel):
    enabled: bool


class McpResourceRead(BaseModel):
    uri: str = Field(min_length=1, max_length=4000)


class McpPromptGet(BaseModel):
    name: str = Field(min_length=1, max_length=400)
    arguments: dict[str, str] = Field(default_factory=dict)
```

- [ ] **Step 2: Write API tests**

Use the existing `TestClient`/`require_auth` override pattern. Mock
`mcp_manager.connect_and_discover` to return a deterministic snapshot and
assert:

- create saves and returns `status` plus masked secret flags;
- raw secret text is absent from every response;
- refresh calls discovery;
- unknown server/item returns 404;
- disabled server/item toggle or resource/prompt action returns 409;
- resource read and prompt get return normalized manager values;
- delete calls `close_server` and removes persisted items.

- [ ] **Step 3: Run API tests to verify failure**

Run:

```bash
uv run pytest tests/integration/test_mcp_api.py -q
```

Expected: FAIL because schemas/routes do not exist.

- [ ] **Step 4: Implement API routes**

Persist the request before attempting discovery. For enabled create/update,
call `await mcp_manager.connect_and_discover(server_id)` and return the safe
server row with `status="error"` if connection fails. Use HTTP 400 for invalid
configuration, 404 for unknown IDs, and 409 for disabled server/item actions.

For resource reads and prompt gets, require both the server and selected item
to be globally enabled, then call the manager. Never pass raw secret maps into
the response serializer.

- [ ] **Step 5: Wire lifespan cleanup**

In `app/main.py`, import the manager lazily in `_lifespan`, call
`await mcp_manager.close_all()` in the `finally` block after scheduler and
Telegram shutdown, and leave startup non-blocking. Store status reset happens
in `Store._open()` from Task 3.

- [ ] **Step 6: Run API and regression tests**

Run:

```bash
uv run pytest tests/integration/test_mcp_api.py tests/integration/test_llm_profiles_api.py tests/unit/models/test_mcp.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add app/schemas/mcp.py app/schemas/__init__.py app/api/platform.py app/main.py tests/integration/test_mcp_api.py
git commit -m "feat: expose MCP server API"
```

---

### Task 9: Build the System → MCP configuration UI

**Files:**
- Create: `app/templates/partials/settings/mcp.html`
- Create: `app/static/js/mcp.js`
- Modify: `app/templates/system.html`
- Modify: `app/templates/partials/settings/nav.html`
- Modify: `app/static/js/system.js`
- Modify: `app/web/pages.py`
- Modify: `app/static/css/tomo.css`
- Test: `tests/integration/test_mcp_api.py` or a new `tests/unit/web/test_mcp_page.py`

**Interfaces:**
- Consumes: Task 8 API routes and `store.list_mcp_servers()` page data.
- Produces a server-rendered System → MCP section with client-side CRUD and
  capability controls.

- [ ] **Step 1: Add page data and navigation**

Pass `mcp_servers=store.list_mcp_servers()` from `system_page()` into
`system.html`. Include `partials/settings/mcp.html`, add a `#mcp` nav link, and
allow `mcp` in `system.js` hash validation. Load `mcp.js` after `modules.js`
and before `system.js`.

- [ ] **Step 2: Add the form and server list markup**

The partial must include:

- `#sec-mcp` and `#mcpServerList`;
- `#addMcpServerBtn`;
- `#mcpFormCard` with hidden mode/id fields;
- transport select with `stdio` and `streamable_http`;
- conditional command/args/env and URL/headers fields;
- enabled toggle, Cancel, Save, and Refresh/Test controls;
- capability containers for tools/resources/prompts.

Use `type="password"` for secret values and never place decrypted values in
the template context.

- [ ] **Step 3: Implement `mcp.js` list/form actions**

Use the existing `Tomo.api`, `Tomo.escapeHtml`, and `Tomo.toast` conventions.
Implement dynamic key/value row add/remove helpers, form validation, server
list rendering, edit/delete, enable toggle, and save. On edit, render only
secret keys and masked placeholders; send `••••` for unchanged values.

After save or refresh, reload the detail row and show status/error text without
throwing away the saved configuration on connection failure.

- [ ] **Step 4: Implement capability panels**

Render each capability with safe escaped metadata and a global toggle. Wire
item toggles to `PUT /items/{item_id}`. Resource “Read” calls the resource API
and displays bounded text/blob summaries. Prompt “Use” opens argument inputs,
calls the prompt API, and displays the normalized messages for copying or
composer insertion.

- [ ] **Step 5: Add focused styles and page assertion**

Add only MCP-specific selectors to `tomo.css`, reusing existing cards, rows,
badges, toggles, and config-grid styles. Assert `/system` contains `MCP`,
`sec-mcp`, and the server form controls in the page test.

- [ ] **Step 6: Run page/API checks**

Run:

```bash
uv run pytest tests/integration/test_mcp_api.py tests/unit/models/test_users.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add app/templates/system.html app/templates/partials/settings/nav.html app/templates/partials/settings/mcp.html app/static/js/system.js app/static/js/mcp.js app/static/css/tomo.css app/web/pages.py tests/integration/test_mcp_api.py
git commit -m "feat: add System MCP configuration UI"
```

---

### Task 10: Surface MCP tools in Agent Studio and prompts/resources in chat

**Files:**
- Modify: `app/templates/partials/agent_studio/panel_tools.html`
- Modify: `app/static/js/agent_detail.js` only if the existing save selector needs MCP-specific disabled handling
- Modify: `app/templates/partials/chat_composer.html`
- Modify: `app/static/js/chat.js`
- Modify: `app/static/css/tomo.css`
- Test: `tests/unit/runtime/tools/test_mcp_catalog.py`
- Test: `tests/integration/test_mcp_api.py`

**Interfaces:**
- Consumes: Task 6 merged agent catalog and Task 8 resource/prompt routes.
- Produces source-labelled Agent Studio rows, prompt selection in the slash
  menu, and a composer resource action.

- [ ] **Step 1: Add source/disabled rendering to Agent Studio**

For `t.backend` beginning with `mcp:`, render a small source badge using the
server name. Set the toggle disabled when `t.locked` is true. Keep the existing
`data-tool-id`, `.tool-enable`, and `toolsSave` selectors so the existing
`PUT /api/agents/{agent_id}/tools` payload automatically includes MCP IDs.

- [ ] **Step 2: Add prompt cache and slash-menu entries**

In `chat.js`, keep the existing skill cache and add an MCP prompt cache loaded
from enabled server detail endpoints. Represent entries as:

```javascript
{
  kind: 'mcp_prompt',
  serverId: 'github',
  itemId: 'item-id',
  id: 'github/review_code',
  name: 'review_code',
  description: 'Review code'
}
```

Merge prompt entries into `filterSkills()` ranking and mark them in
`renderSlashMenu()`. Selecting an MCP prompt must call `prompts/get`, collect
required arguments with a small prompt dialog, flatten text messages, and set
the returned text into the composer. Do not insert a slash token and do not
auto-send; this avoids changing `services/chat.py` skill expansion semantics.

- [ ] **Step 3: Add resource picker action**

Add a `Resources`/`MCP` action to the composer’s existing mobile-more actions.
Load enabled resources from the server endpoints, let the user choose one, call
`resources/read`, and insert a visibly-marked text context block into the input.
For binary content, show the MIME/size summary and a link/action rather than
inserting base64.

- [ ] **Step 4: Add UI tests at the API boundary**

Extend the MCP integration test to assert the prompt/resource response shape
consumed by `chat.js`, and assert the Agent Studio page includes an MCP source
badge for a seeded MCP tool. Keep JavaScript behavior covered by deterministic
API fixtures and a syntax check rather than introducing a browser test
framework to this repository.

- [ ] **Step 5: Run focused UI/runtime tests**

Run:

```bash
uv run pytest tests/unit/runtime/tools/test_mcp_catalog.py tests/integration/test_mcp_api.py -q
node --check app/static/js/mcp.js
node --check app/static/js/chat.js
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/templates/partials/agent_studio/panel_tools.html app/templates/partials/chat_composer.html app/static/js/agent_detail.js app/static/js/chat.js app/static/css/tomo.css tests/unit/runtime/tools/test_mcp_catalog.py tests/integration/test_mcp_api.py
git commit -m "feat: use MCP capabilities in agent and chat UI"
```

---

### Task 11: Add transport-level integration fixtures and finish regression coverage

**Files:**
- Create: `tests/fakes/mcp_server.py`
- Modify: `tests/unit/runtime/mcp/test_manager.py`
- Modify: `tests/integration/test_mcp_api.py`
- Add/modify: `tests/integration/test_chat_sse.py` only for an MCP tool event case

**Interfaces:**
- Consumes: Tasks 4–10 complete manager, API, and chat interfaces.
- Produces deterministic local coverage for both selected transports and the
  full user-visible tool flow.

- [ ] **Step 1: Add an SDK-backed stdio fixture**

Create a tiny executable test server using the official SDK that exposes:

- `echo` tool with an object input schema;
- one text resource;
- one prompt with a required string argument.

It must run under `if __name__ == "__main__":` with `mcp.run(transport="stdio")`
and write no non-protocol output to stdout.

- [ ] **Step 2: Test stdio discovery and call**

Configure a `stdio` server pointing at the fixture, run
`connect_and_discover`, call the namespaced `echo` tool, and assert the text
result and stored resource/prompt rows.

- [ ] **Step 3: Test Streamable HTTP with an injected client**

Use an SDK-backed ASGI test app or injected `httpx` transport, not a public URL.
Assert the manager sends the configured custom header and can initialize,
discover, and call the same `echo` tool over Streamable HTTP.

- [ ] **Step 4: Test full chat tool flow**

Use the existing scripted LLM fake to return a namespaced MCP tool call. Mock
the manager result, run a session turn, and assert the persisted `tool_call`,
`tool_output`, and SSE event all contain the friendly MCP call/result without
leaking configured secrets.

- [ ] **Step 5: Run the complete relevant suite**

Run:

```bash
uv run pytest tests/unit/models/test_schema.py tests/unit/models/test_mcp.py tests/unit/runtime/mcp tests/unit/runtime/tools/test_mcp_catalog.py tests/unit/runtime/tools/test_registry.py tests/integration/test_mcp_api.py tests/integration/test_chat_mock.py tests/integration/test_chat_sse.py tests/integration/test_permissions_chat.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add tests/fakes/mcp_server.py tests/unit/runtime/mcp tests/integration/test_mcp_api.py tests/integration/test_chat_sse.py
git commit -m "test: cover MCP transports and chat flow"
```

---

### Task 12: Run repository verification and update user-facing docs

**Files:**
- Modify: `README.md` in Configuration/Architecture sections

**Interfaces:**
- Consumes: all completed MCP behavior.
- Produces concise installation/configuration documentation and a verified
  repository state.

- [ ] **Step 1: Document MCP configuration**

Add a short README section explaining that System → MCP supports local
`stdio` and remote Streamable HTTP, that headers/environment values are
encrypted, that servers/tools can be disabled, and that agent tools are
controlled independently. Explicitly distinguish this MCP integration from
the existing Tomo Connector tunnel binary.

- [ ] **Step 2: Run formatting/lint checks**

Run:

```bash
uv run ruff check app cli modules tests
uv run pytest
node --check app/static/js/mcp.js
node --check app/static/js/chat.js
```

Expected: all commands exit 0.

- [ ] **Step 3: Inspect the final diff**

Run:

```bash
git diff --check
git status --short
git diff --stat origin/main...HEAD
```

Confirm no secret values, generated databases, caches, or unrelated `.gitignore`
changes are staged.

- [ ] **Step 4: Commit documentation and final verification record**

```bash
git add README.md
git commit -m "docs: document MCP configuration"
```

---

## Plan self-review

- Spec coverage: transport dependency (Task 1), schema (Task 2), encrypted
  persistence (Task 3), pagination/namespacing/results (Task 4), live sessions
  and reconnect (Task 5), catalog/dispatch (Task 6), permissions and pre-turn
  reconnect (Task 7), API/lifecycle (Task 8), System UI (Task 9), Agent Studio
  and chat capabilities (Task 10), transport/chat integration tests (Task 11),
  and documentation/regression checks (Task 12).
- Placeholder scan: no `TODO`, `TBD`, or unspecified implementation steps are
  used in the tasks.
- Type consistency: all later tasks consume the exact Store, manager, discovery,
  and registry interfaces defined earlier; `connected_server_ids` is passed
  explicitly into schema construction after the async pre-turn reconnect.
- Scope check: no OAuth, legacy SSE, sampling, elicitation, roots, or unrelated
  refactoring is included.
